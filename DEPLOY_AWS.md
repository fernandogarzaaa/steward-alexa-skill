# Deploy Steward on AWS (EC2 + Bedrock Nova Micro)

Step-by-step runbook for deploying the Steward hosted demo to AWS. Each
step is a copy-paste command for AWS CLI v2. Run them in order from a
shell that has AWS credentials for the target account.

## Decision

| | |
|---|---|
| Service | **Amazon EC2, one `t4g.micro`** (Graviton/arm64, 2 vCPU, 1 GiB), Amazon Linux 2023, Docker |
| Region | `us-east-1` (Nova Micro on-demand; cheapest t4g pricing) |
| Model | Amazon Bedrock `amazon.nova-micro-v1:0`, called with the **instance's IAM role** (no access keys) |
| Stable address | **Elastic IP**, giving `http://<EIP>/`. Optional HTTPS at `https://<EIP-dashed>.sslip.io` (step 9) |
| Health check | `GET /health` returns `{"ok":true}` (no model or AWS calls) |
| Admin access | SSM Session Manager (no SSH key, no port 22) |
| Expected cost | **about $10.60/month** plus Bedrock tokens (cost table at the end) |

Why not the alternatives:
- **App Runner** stopped accepting new customers on 2026-04-30
  (https://aws.amazon.com/apprunner/).
- **Lightsail** (a $7/month 1 GB instance with static IP, or a $7/month
  Nano container service) is cheaper, but it does not support IAM roles,
  so Bedrock would need long-lived access keys on the host. Use it only if
  the IAM-role requirement is dropped.
- **t4g.nano** (0.5 GiB, about $3/month less) is too tight for the OS,
  Docker, and two Python processes with live agent sessions.

## 0. Prerequisites

- AWS CLI v2 configured for the target account. The caller needs IAM,
  EC2, and SSM permissions.
- **The code must be reachable from the instance.** The `hosted-demo`
  branch exists only in a local clone and has not been pushed yet. Before
  step 4, either push it to
  `https://github.com/fernandogarzaaa/steward-alexa-skill` (the repo
  owner decides this), or follow the "No push" note in step 4.
- Bedrock model access: Amazon Bedrock enables serverless models
  automatically, so the Model Access page is gone. Nova is a first-party
  model, so no Marketplace subscription is needed. If the account has an
  SCP that blocks Bedrock, fix that first.

Set shell variables once:

```bash
export AWS_REGION=us-east-1
export AWS_DEFAULT_REGION=us-east-1
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export NAME=steward-demo
export REPO_URL=https://github.com/fernandogarzaaa/steward-alexa-skill.git
export BRANCH=hosted-demo
```

## 1. IAM role for Bedrock Nova Micro (+ SSM)

```bash
cat > /tmp/trust.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "ec2.amazonaws.com"},
    "Action": "sts:AssumeRole"
  }]
}
EOF

# Least privilege: only Nova Micro, only in us-east-1. Strands calls
# Converse/ConverseStream, which authorize as these two actions.
cat > /tmp/bedrock-nova-micro.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "InvokeNovaMicroOnly",
    "Effect": "Allow",
    "Action": [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream"
    ],
    "Resource": "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-micro-v1:0"
  }]
}
EOF

aws iam create-role --role-name ${NAME}-role \
  --assume-role-policy-document file:///tmp/trust.json
aws iam put-role-policy --role-name ${NAME}-role \
  --policy-name bedrock-nova-micro \
  --policy-document file:///tmp/bedrock-nova-micro.json
aws iam attach-role-policy --role-name ${NAME}-role \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
aws iam create-instance-profile --instance-profile-name ${NAME}-profile
aws iam add-role-to-instance-profile \
  --instance-profile-name ${NAME}-profile --role-name ${NAME}-role
sleep 15   # IAM propagation before run-instances can use the profile
```

If you later switch to the cross-region profile `us.amazon.nova-micro-v1:0`,
add `arn:aws:bedrock:*::foundation-model/amazon.nova-micro-v1:0` and
`arn:aws:bedrock:us-east-1:${ACCOUNT_ID}:inference-profile/us.amazon.nova-micro-v1:0`
to `Resource`. The default setup does not need either.

## 2. Security group (HTTP only, no SSH)

```bash
export VPC_ID=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true \
  --query 'Vpcs[0].VpcId' --output text)
export SG_ID=$(aws ec2 create-security-group --group-name ${NAME}-sg \
  --description "Steward demo: HTTP/HTTPS" --vpc-id $VPC_ID \
  --query GroupId --output text)
aws ec2 authorize-security-group-ingress --group-id $SG_ID \
  --ip-permissions \
  'IpProtocol=tcp,FromPort=80,ToPort=80,IpRanges=[{CidrIp=0.0.0.0/0}]' \
  'IpProtocol=tcp,FromPort=443,ToPort=443,IpRanges=[{CidrIp=0.0.0.0/0}]'
```

If the account has no default VPC, create one with
`aws ec2 create-default-vpc` or pass an existing public subnet with
`--subnet-id` in step 4.

## 3. User data (installs Docker, builds, runs the container)

```bash
cat > /tmp/userdata.sh <<EOF
#!/bin/bash
set -euxo pipefail
dnf install -y docker git
systemctl enable --now docker
# 1 GiB swap: headroom for the image build and agent sessions on 1 GiB RAM
fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile
swapon /swapfile && echo '/swapfile swap swap defaults 0 0' >> /etc/fstab
git clone --depth 1 --branch ${BRANCH} ${REPO_URL} /opt/steward
docker build -t steward:latest /opt/steward
docker run -d --name steward --restart unless-stopped \\
  -p 80:8080 -v steward-data:/data \\
  -e STEWARD_MODEL_PROVIDER=bedrock \\
  -e STEWARD_BEDROCK_MODEL_ID=amazon.nova-micro-v1:0 \\
  -e STEWARD_AWS_REGION=us-east-1 \\
  -e AWS_REGION=us-east-1 \\
  -e STEWARD_DEMO_MODE=1 \\
  -e STEWARD_DEMO_RESET_HOURS=6 \\
  steward:latest
EOF
```

(`${BRANCH}` and `${REPO_URL}` expand now. The escaped `\\` become line
continuations in the script.)

## 4. Launch the instance

```bash
export INSTANCE_ID=$(aws ec2 run-instances \
  --image-id resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64 \
  --instance-type t4g.micro \
  --iam-instance-profile Name=${NAME}-profile \
  --security-group-ids $SG_ID \
  --metadata-options HttpTokens=required,HttpPutResponseHopLimit=2,HttpEndpoint=enabled \
  --block-device-mappings 'DeviceName=/dev/xvda,Ebs={VolumeSize=10,VolumeType=gp3,DeleteOnTermination=true}' \
  --credit-specification CpuCredits=standard \
  --user-data file:///tmp/userdata.sh \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=${NAME}}]" \
  --query 'Instances[0].InstanceId' --output text)
aws ec2 wait instance-running --instance-ids $INSTANCE_ID
```

Critical flags:
- `HttpPutResponseHopLimit=2`: the container reaches the instance metadata
  service through Docker's bridge, which is one extra hop. With the default
  limit of 1, boto3 inside the container finds **no IAM credentials** and
  `/api/chat` returns 503 "No model configured".
- `CpuCredits=standard`: no surprise unlimited-mode CPU credit charges.

**No push?** Launch without `--user-data`, then copy the code over SSM.
For example, create a `git bundle create steward.bundle hosted-demo` and
put it in a private S3 bucket. Adding S3 read to the role makes that work.
Then run the step 3 script on the box with `git clone steward.bundle`
in place of the GitHub URL.

## 5. Static IP (Elastic IP)

```bash
export ALLOC_ID=$(aws ec2 allocate-address --domain vpc \
  --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Name,Value=${NAME}}]" \
  --query AllocationId --output text)
aws ec2 associate-address --instance-id $INSTANCE_ID --allocation-id $ALLOC_ID
export EIP=$(aws ec2 describe-addresses --allocation-ids $ALLOC_ID \
  --query 'Addresses[0].PublicIp' --output text)
echo "Demo URL: http://$EIP/"
```

## 6. Verify (the build takes about 5 to 8 minutes after boot)

```bash
# Liveness: expect {"ok":true}
until curl -fsS --max-time 5 http://$EIP/health; do sleep 15; done; echo
# Model wiring: expect "model":"bedrock:amazon.nova-micro-v1:0" and "demo":{"reset_hours":6.0}
curl -s http://$EIP/api/health; echo
# UI: expect 200
curl -s -o /dev/null -w "%{http_code}\n" http://$EIP/
# End-to-end through Bedrock: expect a reply plus tool_calls including clock_now
curl -s -X POST http://$EIP/api/chat -H 'content-type: application/json' \
  -d '{"message":"What time is it, and remind me to call mom tomorrow"}'; echo
```

Troubleshooting with SSM (no SSH):

```bash
aws ssm start-session --target $INSTANCE_ID
# on the box:
sudo tail -n 100 /var/log/cloud-init-output.log   # user-data / build log
sudo docker logs --tail 100 steward               # app log
```

| Symptom | Cause / fix |
|---|---|
| `/api/chat` 503 "No model configured" | Hop limit is 1 or the profile is not attached. Run `aws ec2 modify-instance-metadata-options --instance-id $INSTANCE_ID --http-put-response-hop-limit 2 --http-tokens required`, then `sudo docker restart steward`. |
| `/api/chat` 502 `AccessDeniedException` | The inline policy is missing, or the model id or region does not match the policy ARN. |
| `/api/chat` 502 `ValidationException ... on-demand throughput isn't supported` | Use the inference profile ARN and id as described in the note after step 1. |
| `/health` never answers | Check the build in `cloud-init-output.log`. Out of memory during `pip install` means the swap is missing. |

## 7. Health monitoring and auto-recovery

The container restarts itself: `scripts/start.sh` exits if either process
dies, and `--restart unless-stopped` brings it back. Add an EC2
auto-recover alarm for host failures:

```bash
aws cloudwatch put-metric-alarm --alarm-name ${NAME}-recover \
  --namespace AWS/EC2 --metric-name StatusCheckFailed_System \
  --dimensions Name=InstanceId,Value=$INSTANCE_ID \
  --statistic Maximum --period 60 --evaluation-periods 2 \
  --threshold 1 --comparison-operator GreaterThanOrEqualToThreshold \
  --alarm-actions arn:aws:automate:${AWS_REGION}:ec2:recover
```

External uptime checks should poll `http://$EIP/health` (cheap, no model
call). Do not poll `/api/health`, because it resolves AWS credentials on
every call.

Recommended cost guard: an AWS Budget of $15/month with an email alert.
Creating one needs an email address, so ask the account owner which
address to use.

## 8. Redeploy after a code change

```bash
aws ssm send-command --instance-ids $INSTANCE_ID \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["set -e","cd /opt/steward && git pull --ff-only","docker build -t steward:latest /opt/steward","docker rm -f steward","docker run -d --name steward --restart unless-stopped -p 80:8080 -v steward-data:/data -e STEWARD_MODEL_PROVIDER=bedrock -e STEWARD_BEDROCK_MODEL_ID=amazon.nova-micro-v1:0 -e STEWARD_AWS_REGION=us-east-1 -e AWS_REGION=us-east-1 -e STEWARD_DEMO_MODE=1 -e STEWARD_DEMO_RESET_HOURS=6 steward:latest"]'
```

## 9. Optional: HTTPS on a stable hostname (no domain needed)

The demo cookie is sent `Secure` automatically when the request arrives
over HTTPS. Caddy gets a Let's Encrypt certificate for the free
`sslip.io` name that maps to the Elastic IP:

```bash
HOST="$(echo $EIP | tr . -).sslip.io"     # e.g. 3-91-10-20.sslip.io
aws ssm send-command --instance-ids $INSTANCE_ID \
  --document-name AWS-RunShellScript \
  --parameters "commands=[\"docker rm -f steward\",\"docker network create web || true\",\"docker run -d --name steward --network web --restart unless-stopped -v steward-data:/data -e STEWARD_MODEL_PROVIDER=bedrock -e STEWARD_BEDROCK_MODEL_ID=amazon.nova-micro-v1:0 -e STEWARD_AWS_REGION=us-east-1 -e AWS_REGION=us-east-1 -e STEWARD_DEMO_MODE=1 -e STEWARD_DEMO_RESET_HOURS=6 steward:latest\",\"docker run -d --name caddy --network web --restart unless-stopped -p 80:80 -p 443:443 -v caddy-data:/data caddy:2 caddy reverse-proxy --from $HOST --to steward:8080\"]"
echo "https://$HOST/"
```

For a custom domain, point an A record at `$EIP` and use that name in
`--from`.

## Environment variables (container)

| Variable | Value | Notes |
|---|---|---|
| `STEWARD_MODEL_PROVIDER` | `bedrock` | Forces Bedrock |
| `STEWARD_BEDROCK_MODEL_ID` | `amazon.nova-micro-v1:0` | Must match the IAM policy ARN |
| `STEWARD_AWS_REGION` | `us-east-1` | Bedrock region |
| `AWS_REGION` | `us-east-1` | For boto3 generally |
| `STEWARD_DEMO_MODE` | `1` | Per-browser isolation (already the image default) |
| `STEWARD_DEMO_RESET_HOURS` | `6` | Visitor data wiped every 6 h (image default) |
| `PORT` | `8080` (image default) | Container port. Host port 80 maps to it |
| `STEWARD_DB` | unset | `start.sh` uses `/data/steward.db` (the `steward-data` volume) |
| Optional | `STEWARD_DEMO_MAX_SESSIONS=25`, `STEWARD_DEMO_IDLE_MINUTES=60`, `STEWARD_DEMO_MAX_CHARS=1000` | Abuse and cost limits |

No AWS access keys. Credentials come from the instance profile through
IMDSv2.

## Expected monthly cost (us-east-1, on-demand, 730 h)

| Item | Rate | Month |
|---|---|---|
| EC2 t4g.micro | $0.0084/h | $6.13 |
| Public IPv4 (Elastic IP, in use) | $0.005/h | $3.65 |
| EBS gp3 root, 10 GB | $0.08/GB-month | $0.80 |
| Data transfer out | first 100 GB/month free across the account | $0 |
| CloudWatch recover alarm | 1 standard alarm (10 free per month) | $0 |
| **Infrastructure total** | | **about $10.58** |
| Bedrock Nova Micro | $0.035 per 1M input, $0.14 per 1M output tokens | about $0.50 per 1,000 chat turns (estimate: about 10k input and 500 output tokens per agentic turn) |

Sources: EC2 t4g pricing (https://aws.amazon.com/ec2/pricing/on-demand/,
cross-checked with third-party price trackers), IPv4 and EBS pricing
(https://aws.amazon.com/vpc/pricing/, https://aws.amazon.com/ebs/pricing/),
and Nova Micro token pricing (https://aws.amazon.com/bedrock/pricing/,
matching OpenRouter's listing). Prices as of October 2026. A 1-year
no-upfront Savings Plan or RI drops the instance to about $3.87/month.

## Teardown

```bash
aws ec2 disassociate-address --association-id $(aws ec2 describe-addresses \
  --allocation-ids $ALLOC_ID --query 'Addresses[0].AssociationId' --output text)
aws ec2 release-address --allocation-id $ALLOC_ID      # idle EIPs still bill $3.65/mo
aws ec2 terminate-instances --instance-ids $INSTANCE_ID
aws ec2 wait instance-terminated --instance-ids $INSTANCE_ID
aws ec2 delete-security-group --group-id $SG_ID
aws cloudwatch delete-alarms --alarm-names ${NAME}-recover
aws iam remove-role-from-instance-profile --instance-profile-name ${NAME}-profile --role-name ${NAME}-role
aws iam delete-instance-profile --instance-profile-name ${NAME}-profile
aws iam detach-role-policy --role-name ${NAME}-role --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
aws iam delete-role-policy --role-name ${NAME}-role --policy-name bedrock-nova-micro
aws iam delete-role --role-name ${NAME}-role
```
