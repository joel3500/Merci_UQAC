from datetime import datetime
from peewee import Model, CharField, TextField, DateTimeField
from database import db


class BaseModel(Model):
    class Meta:
        database = db


class ChatMessage(BaseModel):
    prenom = CharField(max_length=50)
    programme = CharField(max_length=120)
    etablissement_scolaire = CharField(max_length=180, null=True)
    commentaire = TextField()
    created_at = DateTimeField(default=datetime.utcnow)
