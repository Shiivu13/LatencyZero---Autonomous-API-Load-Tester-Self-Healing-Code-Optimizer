from optimizer.offline import find_issues, propose


def kinds(code):
    return [i.kind for i in find_issues(code)]


def test_detects_blocking_call_in_async():
    code = "import time\nasync def f():\n    time.sleep(1)\n"
    assert kinds(code) == ["blocking_call_in_async"]


def test_blocking_call_in_sync_function_is_milder():
    code = "import requests\ndef f():\n    return requests.get('http://x')\n"
    assert kinds(code) == ["blocking_call"]


def test_detects_query_in_loop():
    code = (
        "def f(cur, ids):\n"
        "    for i in ids:\n"
        "        cur.execute('SELECT * FROM t WHERE id = ?', (i,))\n"
    )
    assert kinds(code) == ["query_in_loop"]


def test_detects_list_membership_in_loop():
    code = (
        "def f(items):\n"
        "    seen = []\n"
        "    for x in items:\n"
        "        if x not in seen:\n"
        "            seen.append(x)\n"
    )
    assert kinds(code) == ["list_membership_in_loop"]


def test_nested_sync_function_not_flagged_as_async():
    code = (
        "import time\n"
        "async def outer():\n"
        "    def inner():\n"
        "        time.sleep(1)\n"
        "    return inner\n"
    )
    assert kinds(code) == ["blocking_call"]


def test_rewrites_requests_with_to_thread():
    code = (
        "import requests\n"
        "async def f(user):\n"
        "    r = requests.get(f'https://api/{user}', timeout=5)\n"
        "    return r.json()\n"
    )
    proposal = propose(code)
    assert proposal.source == "offline:rewrite"
    assert "await asyncio.to_thread(requests.get, f'https://api/{user}', timeout=5)" in proposal.optimized_code
    assert proposal.optimized_code.startswith("import asyncio\n")
    compile(proposal.optimized_code, "<test>", "exec")


def test_rewrite_keeps_comments_and_other_lines():
    code = (
        "import asyncio\n"
        "import time\n"
        "async def f():\n"
        "    # wait a bit\n"
        "    time.sleep(0.5)  # é\n"
        "    return 1\n"
    )
    proposal = propose(code)
    assert proposal.optimized_code == code.replace("time.sleep(0.5)", "await asyncio.sleep(0.5)")


def test_no_rewrite_returns_code_unchanged():
    code = "def f():\n    return 1\n"
    proposal = propose(code)
    assert proposal.source == "offline:none"
    assert proposal.optimized_code == code
