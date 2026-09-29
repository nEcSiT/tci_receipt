from django.http import JsonResponse
from django.db import connection
from django.core.cache import cache
import logging

logger = logging.getLogger(__name__)


def health_check(request):
    """
    Readiness and liveness probe checking:
    1. Application process
    2. Database connectivity
    3. Cache/Redis connectivity
    """
    status = {"status": "healthy", "components": {}}
    http_status = 200

    # 1. Database check
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1;")
            row = cursor.fetchone()
            if row and row[0] == 1:
                status["components"]["database"] = "healthy"
            else:
                status["components"]["database"] = "unhealthy"
                http_status = 503
    except Exception as e:
        logger.error(f"Healthcheck database error: {e}")
        status["components"]["database"] = f"unhealthy: {str(e)}"
        http_status = 503

    # 2. Cache / Redis check (graceful)
    try:
        cache.set("healthcheck_test_key", "ok", 10)
        cached_val = cache.get("healthcheck_test_key")
        if cached_val == "ok":
            status["components"]["cache"] = "healthy"
        else:
            status["components"]["cache"] = "degraded"
    except Exception as e:
        status["components"]["cache"] = f"unhealthy: {str(e)}"

    if http_status != 200:
        status["status"] = "unhealthy"

    return JsonResponse(status, status=http_status)
