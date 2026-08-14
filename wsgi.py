import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from database import init_db
from app import app as application

init_db()
