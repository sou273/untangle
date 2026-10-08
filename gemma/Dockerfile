# Untangle の伏せる層で使う Gemma（非公開の Cloud Run。画面のサービスだけが呼べる）
FROM ollama/ollama:latest
ENV OLLAMA_HOST=0.0.0.0:8080 \
    OLLAMA_MODELS=/models \
    OLLAMA_KEEP_ALIVE=-1 \
    OLLAMA_NUM_PARALLEL=4
# モデルをイメージに焼き込む（起動のたびにダウンロードしない）
RUN sh -c "ollama serve & pid=\$!; sleep 5; ollama pull gemma3:4b && kill \$pid"
EXPOSE 8080
ENTRYPOINT ["ollama", "serve"]
