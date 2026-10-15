# Fixture: the symbolic-chmod shape for lint_dockerfile_static.py.
# Line 8 below must be flagged by CHECK 2 (chmod octal mode) -- it is
# deliberately broken and must never be fixed here; fixing it would defeat
# the negative test this fixture exists for.

FROM rockylinux:9

COPY --chmod=+x docker/entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
