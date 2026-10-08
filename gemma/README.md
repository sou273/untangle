# gemma（伏せる層の固有名詞検出）

Ollama で gemma3:4b を動かす非公開の Cloud Run サービス。
生の文を見るのはこのサービスだけで、外部の Gemini には伏せた後の文しか渡らない。
呼べるのは Untangle 本体のサービスアカウント（untangle-run）だけ。

```
gcloud builds submit --tag asia-northeast1-docker.pkg.dev/awesome-highway-306814/untangle/gemma:gemma3-4b --machine-type e2-highcpu-8 --timeout 3600
gcloud run deploy untangle-gemma --image asia-northeast1-docker.pkg.dev/awesome-highway-306814/untangle/gemma:gemma3-4b \
  --region asia-northeast1 --no-allow-unauthenticated --cpu 8 --memory 16Gi --max-instances 1 --concurrency 4 --timeout 300
```
