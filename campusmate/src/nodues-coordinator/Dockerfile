FROM python:3.12-slim
WORKDIR /app
COPY . user_agent/
WORKDIR /app/user_agent
# Install framework wheels without their [all] optional extras.
# agent-framework requires agent-framework-core[all], which pulls 20+ optional
# packages from PyPI and causes pip's resolver to exceed 200k rounds.
# --no-deps avoids that; requirements-docker.txt covers all required transitive deps.
RUN if [ -d wheels ]; then pip install --no-cache-dir --no-deps wheels/*.whl; fi
RUN pip install --no-cache-dir -r requirements-docker.txt
EXPOSE 8088 8090
CMD ["python", "main.py"]
