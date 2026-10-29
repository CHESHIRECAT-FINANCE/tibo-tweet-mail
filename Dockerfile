FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml requirements.txt README.md LICENSE ./
COPY info_relay ./info_relay
COPY web ./web
COPY tibo_tweet_mail.py cloud_state.py http_client.py market_digest.py ./
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home relay && mkdir /data && chown relay:relay /data
USER relay
WORKDIR /data
ENTRYPOINT ["inforelay"]
CMD ["watch", "--config", "/config/subscriptions.json", "--state", "/data/state.json"]
