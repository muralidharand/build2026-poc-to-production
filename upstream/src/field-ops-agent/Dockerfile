FROM python:3.12-slim
WORKDIR /app
COPY . user_agent/
WORKDIR /app/user_agent
RUN if [ -d wheels ]; then pip install wheels/*.whl; fi
RUN if [ -f requirements.txt ]; then pip install -r requirements.txt; fi
EXPOSE 8088 8090
CMD ["python", "main.py"]
