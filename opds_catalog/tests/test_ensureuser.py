"""`sopds_util ensureuser` — bootstrapping an account from a deployment.

Django's own createsuperuser fails when the account is already there, which
makes it useless from something that runs on every rollout. This one has to be
safe to run again.
"""
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command


def run(*args):
    out = StringIO()
    call_command('sopds_util', 'ensureuser', *args, stdout=out, stderr=out)
    return out.getvalue()


@pytest.mark.django_db
def test_it_creates_the_account():
    assert 'created' in run('demo', 'secret')

    user = get_user_model().objects.get(username='demo')
    assert user.check_password('secret')
    assert not user.is_superuser


@pytest.mark.django_db
def test_running_it_again_is_not_an_error():
    run('demo', 'secret')
    assert 'password reset' in run('demo', 'secret')
    assert get_user_model().objects.filter(username='demo').count() == 1


@pytest.mark.django_db
def test_it_resets_the_password():
    run('demo', 'first')
    run('demo', 'second')
    assert get_user_model().objects.get(username='demo').check_password('second')


@pytest.mark.django_db
def test_it_can_make_an_administrator():
    run('root', 'secret', '--superuser')
    user = get_user_model().objects.get(username='root')
    assert user.is_superuser and user.is_staff


@pytest.mark.django_db
def test_an_existing_account_can_be_promoted():
    run('root', 'secret')
    run('root', 'secret', '--superuser')
    assert get_user_model().objects.get(username='root').is_superuser


@pytest.mark.django_db
def test_it_refuses_without_a_password_rather_than_making_one_up():
    assert 'Usage' in run('demo')
    assert not get_user_model().objects.filter(username='demo').exists()
