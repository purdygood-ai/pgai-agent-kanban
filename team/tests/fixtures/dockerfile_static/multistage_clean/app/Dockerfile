# Fixture: a well-formed multi-stage Dockerfile for lint_dockerfile_static.py.
# Proves that a second, independent, well-formed FROM line (the
# "FROM <image> AS <name>" three-token shape) does not trip the arity check,
# and that a COPY --from=<stage> flag (unrelated to --chmod) is ignored by
# the chmod check.

FROM node:20-bookworm AS nodejs

FROM python:3.12-bookworm

COPY --from=nodejs /usr/local/bin/node /usr/local/bin/node
COPY --chmod=755 docker/entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
