"""Base declarativa do ORM."""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Nomes deterministicos para indices e constraints.
#
# Sem esta convencao, o banco gera nomes automaticos e inconsistentes entre dialetos --
# e o Alembic, ao comparar o modelo com o banco, nao consegue identificar uma constraint
# existente para altera-la ou remove-la. Definir isso no comeco custa cinco linhas;
# descobrir a falta depois de ter migrations em producao custa muito mais.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Classe base de todos os modelos."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
