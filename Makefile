all: build test push deploy

.PHONY: build test deploy push run

build:
	cp -r Pipfile* src docker/
	docker build -t amackillop/aio-app docker/
	rm -rf docker/Pipfile* docker/src

test:
	echo no tests yet

push:
	docker push amackillop/aio-app

deploy:
	kubectl apply -f kubernetes/deployment.yaml

run:
	docker-compose -f docker/docker-compose.yaml up
	docker-compose -f docker/docker-compose.yaml down
