"""
HTTP Resources
"""
import asyncio
import datetime as dt
import logging
import subprocess
import uuid
from typing import List

from aiohttp import web
from aiohttp.client_exceptions import ClientError
import pymonads as pm
from pymonads import either
from pymonads.utils import identity


from _types import Job, Uploaded
import helpers as hf

logging.basicConfig(level=logging.INFO)

ROUTES = web.RouteTableDef()


@ROUTES.view("/v1/jobs")
class Jobs(web.View):
    def __init__(self, request: web.Request) -> None:
        super().__init__(request)
        self.db = self.request.app["db"]

    async def get(self) -> web.Response:
        """Get a list of submitted jobs."""
        return web.json_response(self.db.keys())

    async def post(self) -> web.Response:
        """Post a job"""
        req = await self.request.json()
        urls = req.get("urls", None)
        if urls is None:
            msg = "Bad Request. No `urls` field."
            return web.Response(status=400, reason=msg)
        job_id = str(uuid.uuid4())
        self._submit_job(job_id, urls)
        return web.json_response(job_id, status=201)

    def _submit_job(self, job_id: str, urls: List[str]) -> None:
        valid, invalid = hf.partition(hf.is_valid_url, urls)
        job = Job(job_id, Uploaded(pending=list(valid), failed=list(invalid)))
        self.db.post(job_id, job.to_dict())
        asyncio.create_task(self._handle_job(job))

    async def _handle_job(self, job: Job) -> None:
        job_id = job.job_id
        self.db.update(job_id, "status", "In-Progress")
        images = await asyncio.gather(
            *[self._handle_download(job_id, url) for url in job.uploaded.pending]
        )
        await asyncio.gather(*[self._upload(image) for image in images])
        self.db.update(job_id, "finished", dt.datetime.utcnow().isoformat())
        self.db.update(job_id, "status", "complete")

    async def _handle_download(self, job_id: str, url: str) -> pm.Either[str]:
        try:
            image = await hf.download_image(url)
        except (ClientError, IOError) as exc:
            logging.info("Failed: %s", url)
            self.db.append(job_id, "uploaded.failed", url)
            self.db.remove(job_id, "uploaded.pending", url)
            return pm.Left(str(exc))

        logging.info("Success: %s", url)
        self.db.append(job_id, "uploaded.completed", url)
        self.db.remove(job_id, "uploaded.pending", url)
        return pm.Right(image)

    @staticmethod
    async def _upload(image: pm.Either[str]) -> pm.Either[str]:
        # Don't actually do anything right now.
        return either.either(identity, identity, image)


@ROUTES.view("/v1/jobs/{job_id}")
class SingleJob(web.View):
    def __init__(self, request: web.Request) -> None:
        super().__init__(request)
        self.db = self.request.app["db"]

    async def get(self) -> web.Response:
        """Check job status"""
        job_id = self.request.match_info.get("job_id", None)
        job = self.db.get(job_id)
        if job is None:
            return web.json_response(
                {"error": f"Job {job_id} was not found."}, status=404
            )
        return web.json_response(job)


@ROUTES.view("/v1/images")
class Images(web.View):
    async def get(self) -> web.Response:
        """Get list of uploaded images"""
        result = subprocess.run(
            "hostname", shell=True, stdout=subprocess.PIPE, check=True
        )
        return web.Response(text=result.stdout.decode())
