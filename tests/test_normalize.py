from tracker.normalize import normalize_address, parse_money


def test_normalize_variants_collapse():
    a = normalize_address("1234 Ocean Park Blvd., Unit 3, Santa Monica, CA 90405", "90405")
    b = normalize_address("1234 OCEAN PARK BOULEVARD #3", "90405")
    assert a == b == "1234 OCEAN PARK BLVD #3 90405"


def test_directionals_and_ordinals():
    assert normalize_address("2711 4th St", "90405") == "2711 4 ST 90405"
    assert normalize_address("100 North Main Street", None) == "100 N MAIN ST"


def test_parse_money():
    assert parse_money("$2,495,000") == 2495000
    assert parse_money("") is None
    assert parse_money(12.5) == 12.5
