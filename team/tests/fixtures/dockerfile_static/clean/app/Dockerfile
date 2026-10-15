# Fixture: a well-formed single-stage Dockerfile for lint_dockerfile_static.py.
# Deliberately minimal -- exercises exactly the two shapes the lint checks
# (a valid single-argument FROM, and an octal COPY --chmod mode) and nothing
# else. Not meant to be built; the lint never invokes a container engine.

FROM rockylinux:9

COPY --chmod=755 docker/entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
