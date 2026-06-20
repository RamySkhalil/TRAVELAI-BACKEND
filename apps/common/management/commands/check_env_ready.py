from django.core.management.base import BaseCommand

from apps.common.services.env_check import collect_environment_report


class Command(BaseCommand):
    help = "Report environment readiness flags without printing any secret values."

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-db-check",
            action="store_true",
            help="Skip the live database connectivity check.",
        )

    def handle(self, *args, **options):
        report = collect_environment_report(check_database=not options["skip_db_check"])

        self.stdout.write(self.style.MIGRATE_HEADING("Environment readiness report"))
        self.stdout.write("This report shows presence/status flags only. No secret values are printed.")
        self.stdout.write("")

        self._line("DEBUG", report["debug"], warn_when=report["debug"])
        self._line("DATABASE_URL present", report["database_url_enabled"], warn_when=not report["database_url_enabled"])
        if "database_connected" in report:
            self._line("Database connectivity", report["database_connected"], warn_when=not report["database_connected"])

        storage = report["storage"]
        self._line("Cloudflare R2 enabled", storage["r2_enabled"])
        if storage["r2_missing_env_names"]:
            self.stdout.write(
                self.style.WARNING(f"  R2 not fully configured. Missing names: {', '.join(storage['r2_missing_env_names'])}")
            )

        self._line("OpenAI configured", report["openai_configured"])
        if report["openai_missing_env_names"]:
            self.stdout.write(
                self.style.WARNING(f"  OpenAI not fully configured. Missing names: {', '.join(report['openai_missing_env_names'])}")
            )

        self._line("ALLOWED_HOSTS configured", report["allowed_hosts_configured"], warn_when=not report["allowed_hosts_configured"])
        self._line("CORS origins configured", report["cors_configured"], warn_when=not report["cors_configured"])

        if not report["debug"] and report["secret_key_is_default"]:
            self.stdout.write(self.style.ERROR("DJANGO_SECRET_KEY is still the development default while DEBUG is off."))
        else:
            self._line("Secret key is non-default", not report["secret_key_is_default"])

        self.stdout.write("")
        blocking_issues = self._blocking_issues(report)
        if blocking_issues:
            self.stdout.write(self.style.ERROR(f"Not ready: {len(blocking_issues)} blocking issue(s)."))
            for issue in blocking_issues:
                self.stdout.write(self.style.ERROR(f"  - {issue}"))
        else:
            self.stdout.write(self.style.SUCCESS("Environment looks ready."))

    def _line(self, label: str, value: bool, *, warn_when: bool = False) -> None:
        marker = "OK " if value else "-- "
        text = f"{marker}{label}: {'yes' if value else 'no'}"
        if warn_when:
            self.stdout.write(self.style.WARNING(text))
        elif value:
            self.stdout.write(self.style.SUCCESS(text))
        else:
            self.stdout.write(text)

    @staticmethod
    def _blocking_issues(report: dict) -> list[str]:
        issues = []
        if report.get("database_connected") is False:
            issues.append("Database is not reachable.")
        if not report["debug"]:
            if report["secret_key_is_default"]:
                issues.append("Production secret key is still the development default.")
            if not report["allowed_hosts_configured"]:
                issues.append("ALLOWED_HOSTS must be configured when DEBUG is off.")
        return issues
