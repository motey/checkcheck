#/bin/bash
docker_tag=$1
if [ -z "$docker_tag" ]; then
    # default container image tag of non is provided
    docker_tag="checkcheck:latest"
fi
echo "Build docker image with tag '$docker_tag'"
docker build . -t $docker_tag -f Dockerfile.server

echo "Docker image produced: $docker_tag"
echo "Run with:"
echo "     docker run $docker_tag"
