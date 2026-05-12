from fastapi import FastAPI
import inngest.fast_api
from deprecated.inngest.agent import inngest_client, run_agent

app = FastAPI()

inngest.fast_api.serve(app, inngest_client, [run_agent])