"""
Image uploading service implemented using asyncio/aiohttp
"""

import asyncio
import base64
import binascii
from dataclasses import asdict
import datetime as dt
import logging
import os
import subprocess
import uuid
from typing import Tuple, List

import uvloop  # type: ignore
import pymonads as pm
import pymonads.either as either
from pymonads.utils import identity
from aiohttp import web
from aiohttp.client_exceptions import ClientError

from my_types import Job, Uploaded
import helpers as hf

logging.basicConfig(level=logging.INFO)
routes = web.RouteTableDef()


# Routes
@routes.get("/v1/jobs")
async def get_jobs(request: web.Request) -> web.Response:
    """Get a list of all submitted jobs"""
    return web.json_response(list(request.app.get("jobs", {}).keys()))


@routes.post("/v1/jobs")
async def submit_job(request: web.Request) -> web.Response:
    """Post a job"""
    req = await request.json()
    urls = req.get("urls", None)
    if urls is None:
        msg = "Bad Request. No `urls` field."
        return web.Response(status=400, reason=msg)
    job_id = str(uuid.uuid4())
    request.app["jobs"][job_id] = _submit_job(job_id, urls)
    return web.json_response(job_id)


@routes.get("/v1/jobs/{job_id}")
async def get_status(request: web.Request) -> web.Response:
    """Check job status"""
    job_id = request.match_info.get("job_id", None)
    job = request.app["jobs"].get(job_id, None)
    if job is None:
        return web.json_response({"error": f"Job {job_id} was not found."}, status=404)
    return web.json_response(asdict(job))


@routes.get("/v1/images")
async def get_images(request: web.Request) -> web.Response:
    """Get list of uploaded images"""
    result = subprocess.run("hostname", shell=True, stdout=subprocess.PIPE, check=True)
    return web.Response(text=result.stdout.decode())


# Helpers
def _submit_job(job_id: str, urls: List[str]) -> Job:
    valid, invalid = hf.partition(hf.is_valid_url, urls)
    job = Job(job_id, Uploaded(pending=list(valid), failed=list(invalid)))
    asyncio.create_task(_handle_job(job))
    return job


async def _handle_job(job: Job) -> None:
    job.status = "in-progress"
    images = await asyncio.gather(
        *[_handle_download(job, url) for url in job.uploaded.pending]
    )
    imgur_links = await asyncio.gather(*[_upload(image) for image in images])
    job.finished = dt.datetime.utcnow().isoformat()
    job.status = "complete"


async def _handle_download(job: Job, url: str) -> pm.Either[str]:
    try:
        image = await hf.download_image(url)
    except (ClientError, IOError) as exc:
        logging.info(f"Failed: {url}")
        job.uploaded.failed.append(url)
        job.uploaded.pending.remove(url)
        return pm.Left(str(exc))

    logging.info(f"Success: {url}")
    job.uploaded.completed.append(url)
    job.uploaded.pending.remove(url)
    return pm.Left(image)


async def _upload(image: pm.Either[str]) -> pm.Either[str]:
    return either.either(identity, _upload_to_imgur, image)


async def _upload_to_imgur(image_as_b64: str) -> pm.Either[web.Response]:
    """Given a base 64 string, upload it as an image tuploado Imgur."""
    url = "https://api.imgur.com/3/image"
    try:
        base64.b64decode(image_as_b64, validate=True)
    except binascii.Error:
        msg = "image_as_b64 needs to be a valid base-64 string."
        return pm.Left(msg)
    data = {"image": image_as_b64}
    headers = {"Authorization": f'Client-ID {os.environ["CLIENT_ID"]}'}
    resp = await hf.make_request("POST", url, headers=headers, data=data)
    return pm.Right(resp)


async def start(app: web.Application, host: str, port: int) -> web.AppRunner:
    """Start the server"""
    runner = web.AppRunner(app)
    await runner.setup()
    server = web.TCPSite(runner, host, port)
    await server.start()
    return runner


def main() -> None:
    """Entrypoint"""
    host = "0.0.0.0"
    port = 8000
    app = web.Application()
    app.add_routes(routes)
    app["jobs"] = dict()
    loop = asyncio.get_event_loop()
    runner = loop.run_until_complete(start(app, host, port))
    print(f"======== Running on http://{host}:8000 ========\n" "(Press CTRL+C to quit)")
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        loop.run_until_complete(runner.cleanup())


if __name__ == "__main__":
    import tracemalloc

    tracemalloc.start()
    uvloop.install()
    main()
