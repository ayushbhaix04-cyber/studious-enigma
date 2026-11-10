from services import captcha


def test_challenge_shape():
    for _ in range(200):
        a, b, opts = captcha.make_challenge()
        assert 1 <= a <= 9 and 1 <= b <= 9
        assert a + b in opts and len(set(opts)) == 4


async def test_correct_answer(redis):
    q, opts = await captcha.issue(redis, 1, 7)
    a, b = (int(x) for x in q.replace("= ?", "").split("+"))
    assert await captcha.verify(redis, 1, 7, a + b) == "ok"


async def test_single_use_and_wrong(redis):
    q, _ = await captcha.issue(redis, 1, 7)
    a, b = (int(x) for x in q.replace("= ?", "").split("+"))
    assert await captcha.verify(redis, 1, 7, a + b + 1) == "wrong"
    assert await captcha.verify(redis, 1, 7, a + b) == "expired"  # consumed: a new sum is required


async def test_ttl_is_five_minutes(redis):
    await captcha.issue(redis, 1, 7, 300)
    assert 0 < await redis.ttl("captcha:1:7") <= 300


async def test_lockout_after_repeated_failures(redis):
    for _ in range(captcha.MAX_FAILS):
        await captcha.issue(redis, 1, 7)
        await captcha.verify(redis, 1, 7, 999)
    assert await captcha.is_locked(redis, 1)
    assert await captcha.verify(redis, 1, 7, 1) == "locked"
