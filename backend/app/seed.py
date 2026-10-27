import argparse
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Category, Comment, Ticket, TicketPriority, TicketStatus, User, UserRole

DEMO_REQUESTER_SUB = "helpdesk-demo-requester"
DEMO_AGENT_SUB = "helpdesk-demo-agent"


@dataclass
class SeedResult:
    # These counts describe inserts from this call, not all rows in the database.
    categories_created: int = 0
    users_created: int = 0
    tickets_created: int = 0
    comments_created: int = 0


def seed_demo_data(session: Session) -> SeedResult:
    """Insert missing demo records; the caller owns commit, rollback and Session lifetime.

    Run serially. This is a local setup tool, not a concurrent upsert operation.
    Existing records are never updated, reactivated, restored or deleted.
    Comments are created only with their new parent ticket, so edits survive reruns.
    """
    result = SeedResult()
    categories: dict[str, Category] = {}
    for name, description in (
        ("Hardware", "Computers, monitors and other physical equipment."),
        ("Software", "Application installation and application errors."),
        ("Network", "Connectivity, Wi-Fi and VPN issues."),
    ):
        category = session.scalars(select(Category).where(Category.name == name)).one_or_none()
        if category is None:
            category = Category(name=name, description=description)
            session.add(category)
            session.flush()
            result.categories_created += 1
        categories[name] = category

    users: dict[UserRole, User] = {}
    for subject, username, role in (
        (DEMO_REQUESTER_SUB, "demo-requester", UserRole.REQUESTER),
        (DEMO_AGENT_SUB, "demo-agent", UserRole.AGENT),
    ):
        user = session.scalars(select(User).where(User.keycloak_sub == subject)).one_or_none()
        if user is None:
            user = User(
                keycloak_sub=subject,
                username=username,
                email=f"{username}@example.invalid",
                role=role,
            )
            session.add(user)
            session.flush()
            result.users_created += 1
        users[role] = user

    requester = users[UserRole.REQUESTER]
    agent = users[UserRole.AGENT]
    ticket_specs = (
        (
            "DEMO-000001",
            "Demo laptop will not start",
            "This synthetic laptop does not respond to its power button.",
            "Hardware",
            TicketPriority.MEDIUM,
            TicketStatus.OPEN,
            False,
        ),
        (
            "DEMO-000002",
            "Demo VPN connection drops",
            "This synthetic VPN connection disconnects during a support session.",
            "Network",
            TicketPriority.HIGH,
            TicketStatus.IN_PROGRESS,
            True,
        ),
        (
            "DEMO-000003",
            "Demo application installation completed",
            "The synthetic application was installed and its launch was verified.",
            "Software",
            TicketPriority.LOW,
            TicketStatus.RESOLVED,
            True,
        ),
    )
    for number, title, description, category_name, priority, status, assigned in ticket_specs:
        # Deliberately include deleted tickets: a rerun must not recreate or restore them.
        existing = session.scalars(select(Ticket).where(Ticket.ticket_number == number)).one_or_none()
        if existing is not None:
            continue

        category = categories[category_name]
        if not category.is_active:
            raise ValueError(f"Demo category {category_name} is inactive; existing data was not changed")
        if not requester.is_active or requester.role != UserRole.REQUESTER:
            raise ValueError("The existing demo requester must be active and have the requester role")
        if assigned and (not agent.is_active or agent.role != UserRole.AGENT):
            raise ValueError("The existing demo agent must be active and have the agent role")

        ticket = Ticket(
            ticket_number=number,
            title=title,
            description=description,
            category_id=category.id,
            requester_id=requester.id,
            assignee_id=agent.id if assigned else None,
            priority=priority,
            status=status,
            resolved_at=datetime.now(UTC) if status == TicketStatus.RESOLVED else None,
        )
        session.add(ticket)
        session.flush()
        result.tickets_created += 1

        if status == TicketStatus.RESOLVED:
            session.add_all(
                [
                    Comment(
                        ticket_id=ticket.id,
                        author_id=agent.id,
                        body="Demo resolution: installation and application launch were verified.",
                        is_internal=False,
                    ),
                    Comment(
                        ticket_id=ticket.id,
                        author_id=agent.id,
                        body="Synthetic internal note: no real employee or device details are stored.",
                        is_internal=True,
                    ),
                ]
            )
            session.flush()
            result.comments_created += 2

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-demo-data", action="store_true", help="Confirm demo inserts into DATABASE_URL")
    arguments = parser.parse_args()
    if not arguments.confirm_demo_data:
        parser.error("Pass --confirm-demo-data after explicitly selecting DATABASE_URL")

    settings = Settings()
    if settings.app_env == "production":
        parser.error("Demo seeding is unavailable in production")
    if make_url(settings.database_url).get_backend_name() != "sqlite":
        parser.error("This local demo command currently supports SQLite only")

    # Defer application Session setup until after argument and target validation.
    from app.db.session import SessionLocal

    try:
        # Success commits once; any exception rolls back the entire seed transaction.
        with SessionLocal.begin() as session:
            result = seed_demo_data(session)
    except (SQLAlchemyError, ValueError) as error:
        if isinstance(error, ValueError):
            parser.exit(1, f"Demo seeding rolled back: {error}\n")
        parser.exit(1, "Demo seeding rolled back. Check that the selected database has been migrated.\n")

    print(
        f"Demo seed committed: {result.categories_created} categories, {result.users_created} users, "
        f"{result.tickets_created} tickets, {result.comments_created} comments created."
    )


if __name__ == "__main__":
    main()
