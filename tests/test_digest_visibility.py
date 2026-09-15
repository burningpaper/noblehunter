"""Members see digest entries for their own artists only; admins see every artist, labelled.

A verdict route must answer exactly like a not-found route for anything on another artist:
same status, same body, and no visible side effect. Visibility is checked before a verdict's
own validation, so a bad verdict on someone else's outreach is still a 404, not a 422 or 500.
"""

import re
from datetime import UTC, date, datetime

import pytest

from core.access import MAX_POSTGRES_INT
from core.digest_view import digest_view
from core.models import Curator, Outreach, Run, RunStageCount
from tests.factories import (
    admin_viewer,
    make_artist,
    make_curator,
    make_member,
    make_outreach,
    make_profile,
    make_user,
    member_viewer,
)
from tests.web_helpers import app_client, csrf_token, member_client, sign_in

NIGHT, EARLIER = date(2026, 9, 15), date(2026, 9, 10)
ARTIST_LABEL = re.compile(r'<span class="digest-group__artist">([^<]*)</span>')


def entry(session, artist, day, brief):
    profile = make_profile(session, artist=artist)
    return make_outreach(session, make_curator(session), profile, day, brief_text=brief)


def admin_client(session):
    client = app_client(session)
    sign_in(client)
    return client


def outreach_snapshot(session, outreach_id: int):
    session.expire_all()
    outreach = session.get(Outreach, outreach_id)
    curator = session.get(Curator, outreach.curator_id)
    return (
        outreach.status,
        outreach.status_changed_at,
        outreach.pitched_at,
        outreach.notes,
        curator.excluded_at,
        curator.exclusion_reason,
    )


def test_a_member_sees_only_their_artists_entries_and_nights(session):
    mine, theirs = make_artist(session, "Mine"), make_artist(session, "Theirs")
    entry(session, mine, NIGHT, "Mine tonight")
    entry(session, theirs, NIGHT, "Theirs tonight")
    entry(session, theirs, EARLIER, "Theirs earlier")

    view = digest_view(session, viewer=member_viewer(mine), digest_date=None, today=NIGHT)

    briefs = [item.brief for group in view.profiles for item in group.entries]
    assert briefs == ["Mine tonight"]
    assert view.earlier_date is None  # their earlier night isn't offered
    assert not view.show_artists


def test_admins_see_every_artist_labelled(session):
    mine, theirs = make_artist(session, "Mine"), make_artist(session, "Theirs")
    entry(session, mine, NIGHT, "Mine tonight")
    entry(session, theirs, NIGHT, "Theirs tonight")

    view = digest_view(session, viewer=admin_viewer(), digest_date=NIGHT, today=NIGHT)

    labels = {group.artist_name for group in view.profiles}
    assert {"Mine", "Theirs"} <= labels
    assert view.show_artists


def test_a_multi_artist_member_sees_labels_too(session):
    mine, also_mine = make_artist(session, "Mine"), make_artist(session, "Also Mine")
    entry(session, mine, NIGHT, "Mine tonight")
    entry(session, also_mine, NIGHT, "Also mine tonight")

    view = digest_view(session, viewer=member_viewer(mine, also_mine), digest_date=NIGHT, today=NIGHT)

    assert view.show_artists


def test_a_member_cannot_record_a_verdict_on_another_artists_entry(session):
    mine, theirs = make_artist(session, "Mine"), make_artist(session, "Theirs")
    other = entry(session, theirs, NIGHT, "Theirs tonight")
    make_member(session, mine, make_user(session, "nik@example.com"))
    client = member_client(session, "nik@example.com")

    response = client.post(
        f"/outreach/{other.id}/verdict",
        data={"verdict": "skip"},
        headers={"x-csrf-token": csrf_token(client), "hx-request": "true"},
    )

    assert response.status_code == 404
    assert session.get(Outreach, other.id).status == "new"


def test_the_digest_page_hides_other_artists(session):
    mine, theirs = make_artist(session, "Mine"), make_artist(session, "Theirs")
    entry(session, mine, NIGHT, "Mine tonight")
    entry(session, theirs, NIGHT, "Theirs tonight")
    make_member(session, mine, make_user(session, "nik@example.com"))

    html = member_client(session, "nik@example.com").get("/digest", headers={"accept": "text/html"}).text

    assert "Mine tonight" in html
    assert "Theirs tonight" not in html


def test_a_single_artist_member_sees_no_artist_label(session):
    mine = make_artist(session, "Mine")
    entry(session, mine, NIGHT, "Mine tonight")
    make_member(session, mine, make_user(session, "nik@example.com"))

    html = member_client(session, "nik@example.com").get("/digest", headers={"accept": "text/html"}).text

    assert "digest-group__artist" not in html


def test_admins_see_artist_labels_rendered_on_the_page(session):
    mine, theirs = make_artist(session, "Mine"), make_artist(session, "Theirs")
    entry(session, mine, NIGHT, "Mine tonight")
    entry(session, theirs, NIGHT, "Theirs tonight")

    html = admin_client(session).get("/digest", headers={"accept": "text/html"}).text

    assert set(ARTIST_LABEL.findall(html)) == {"Mine", "Theirs"}


def test_a_multi_artist_member_sees_artist_labels_rendered_and_not_a_third_artists(session):
    mine, also_mine, theirs = (
        make_artist(session, "Mine"),
        make_artist(session, "Also Mine"),
        make_artist(session, "Theirs"),
    )
    entry(session, mine, NIGHT, "Mine tonight")
    entry(session, also_mine, NIGHT, "Also mine tonight")
    entry(session, theirs, NIGHT, "Theirs tonight")
    nik = make_user(session, "nik@example.com")
    make_member(session, mine, nik)
    make_member(session, also_mine, nik)

    html = member_client(session, "nik@example.com").get("/digest", headers={"accept": "text/html"}).text

    assert set(ARTIST_LABEL.findall(html)) == {"Mine", "Also Mine"}
    assert "Theirs" not in html
    assert "Theirs tonight" not in html


def test_members_dont_see_the_night_in_numbers(session):
    mine = make_artist(session, "Mine")
    entry(session, mine, NIGHT, "Mine tonight")
    make_member(session, mine, make_user(session, "nik@example.com"))

    html = member_client(session, "nik@example.com").get("/digest", headers={"accept": "text/html"}).text

    assert "The night in numbers" not in html


def test_admins_see_the_night_in_numbers_when_there_is_a_run(session):
    mine = make_artist(session, "Mine")
    profile_entry = entry(session, mine, NIGHT, "Mine tonight")
    run = Run(trigger="schedule", status="succeeded", started_at=datetime(2026, 9, 15, 0, 5, tzinfo=UTC))
    session.add(run)
    session.flush()
    session.add(
        RunStageCount(
            run_id=run.id, profile_id=profile_entry.profile_id, stage="digest", count_in=3, count_out=1
        )
    )
    session.flush()

    html = admin_client(session).get("/digest", headers={"accept": "text/html"}).text

    assert "The night in numbers" in html


def test_members_see_admin_only_wording_for_an_empty_digest(session):
    mine = make_artist(session, "Mine")
    make_member(session, mine, make_user(session, "nik@example.com"))

    html = member_client(session, "nik@example.com").get("/digest", headers={"accept": "text/html"}).text

    assert "nightly run finds curators you can reach" in html
    assert "Run now" not in html


def test_admins_see_the_run_now_wording_for_an_empty_digest(session):
    assert "Run now" in admin_client(session).get("/digest", headers={"accept": "text/html"}).text


class TestVerdictVisibility:
    """A member posting a verdict to another artist's outreach must change nothing."""

    @pytest.mark.parametrize("verdict", ["pitched", "skip", "bad-fit", "dead"])
    def test_every_verdict_on_another_artists_outreach_is_refused_and_changes_nothing(self, session, verdict):
        mine, theirs = make_artist(session, "Mine"), make_artist(session, "Theirs")
        other = entry(session, theirs, NIGHT, "Theirs tonight")
        make_member(session, mine, make_user(session, "nik@example.com"))
        client = member_client(session, "nik@example.com")
        before = outreach_snapshot(session, other.id)

        response = client.post(
            f"/outreach/{other.id}/verdict",
            data={"verdict": verdict},
            headers={"x-csrf-token": csrf_token(client), "hx-request": "true"},
        )

        assert response.status_code == 404
        assert outreach_snapshot(session, other.id) == before

    def test_a_nonexistent_id_and_another_artists_id_look_identical(self, session):
        mine, theirs = make_artist(session, "Mine"), make_artist(session, "Theirs")
        other = entry(session, theirs, NIGHT, "Theirs tonight")
        make_member(session, mine, make_user(session, "nik@example.com"))
        client = member_client(session, "nik@example.com")
        headers = {"x-csrf-token": csrf_token(client), "hx-request": "true"}

        missing = client.post("/outreach/999999999/verdict", data={"verdict": "skip"}, headers=headers)
        foreign = client.post(f"/outreach/{other.id}/verdict", data={"verdict": "skip"}, headers=headers)

        assert missing.status_code == foreign.status_code == 404
        assert missing.text == foreign.text

    def test_an_invalid_verdict_on_another_artists_outreach_is_404_not_422(self, session):
        mine, theirs = make_artist(session, "Mine"), make_artist(session, "Theirs")
        other = entry(session, theirs, NIGHT, "Theirs tonight")
        make_member(session, mine, make_user(session, "nik@example.com"))
        client = member_client(session, "nik@example.com")

        response = client.post(
            f"/outreach/{other.id}/verdict",
            data={"verdict": "loved-it"},
            headers={"x-csrf-token": csrf_token(client), "hx-request": "true"},
        )

        assert response.status_code == 404

    def test_an_admin_posting_an_out_of_range_outreach_id_gets_not_found_not_a_crash(self, session):
        client = admin_client(session)

        response = client.post(
            f"/outreach/{MAX_POSTGRES_INT + 1}/verdict",
            data={"verdict": "skip"},
            headers={"x-csrf-token": csrf_token(client), "hx-request": "true"},
        )

        assert response.status_code == 404

    def test_a_members_own_verdict_still_works_and_is_stored(self, session):
        mine = make_artist(session, "Mine")
        mine_outreach = entry(session, mine, NIGHT, "Mine tonight")
        make_member(session, mine, make_user(session, "nik@example.com"))
        client = member_client(session, "nik@example.com")

        response = client.post(
            f"/outreach/{mine_outreach.id}/verdict",
            data={"verdict": "pitched"},
            headers={"x-csrf-token": csrf_token(client), "hx-request": "true"},
        )

        assert response.status_code == 200
        assert session.get(Outreach, mine_outreach.id).status == "pitched"
        assert session.get(Outreach, mine_outreach.id).pitched_at is not None


class TestDigestDayVisibility:
    def test_a_day_with_only_other_artists_entries_shows_nothing_and_isnt_offered(self, session):
        mine, theirs = make_artist(session, "Mine"), make_artist(session, "Theirs")
        entry(session, mine, NIGHT, "Mine tonight")
        entry(session, theirs, EARLIER, "Theirs earlier")
        make_member(session, mine, make_user(session, "nik@example.com"))
        client = member_client(session, "nik@example.com")

        html = client.get(f"/digest/{EARLIER.isoformat()}", headers={"accept": "text/html"}).text
        assert "Theirs earlier" not in html

        latest_html = client.get("/digest", headers={"accept": "text/html"}).text
        assert f"/digest/{EARLIER.isoformat()}" not in latest_html
