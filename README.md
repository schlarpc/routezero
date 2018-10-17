# routezero

Route 53 DNS for ZeroTier networks.

## assumptions

* a Route 53 hosted zone already exists
* ZeroTier network name == Route 53 hosted zone name

## deployment

```
python3 template.py > template.json
aws cloudformation package \
    --template-file template.json \
    --s3-bucket $S3_DEPLOYMENT_BUCKET \
    --use-json \
    --output-template-file packaged.json
aws cloudformation deploy \
    --template-file packaged.json \
    --stack-name RouteZero \
    --parameter-overrides \
        ZerotierApiKey=$ZEROTIER_API_KEY \
        ZerotierNetworkId=$ZEROTIER_NETWORK_ID \
    --capabilities CAPABILITY_IAM \
    --no-fail-on-empty-changeset
```
