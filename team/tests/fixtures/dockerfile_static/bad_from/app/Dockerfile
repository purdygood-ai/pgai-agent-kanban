# Fixture: the duplicated-FROM-keyword shape for lint_dockerfile_static.py.
# Line 6 below must be flagged by CHECK 1 (FROM arity) -- it is
# deliberately broken and must never be fixed here; fixing it would defeat
# the negative test this fixture exists for.

FROM FROM rockylinux:9

COPY --chmod=755 docker/entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
