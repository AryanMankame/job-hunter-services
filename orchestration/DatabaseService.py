from pymongo import MongoClient
import os
from dotenv import load_dotenv
load_dotenv()

class DatabaseService:
    def __init__(self):
        self.__mongo_username = os.getenv("MONGO_USERNAME")
        self.__mongo_password = os.getenv("MONGO_PASSWORD")
        self.__connection_string = f"mongodb+srv://{self.__mongo_username}:{self.__mongo_password}@cluster0.tqm8j4u.mongodb.net/?appName=Cluster0"
        self.__client = MongoClient(self.__connection_string)
        self.__db = self.__client['jobHunter']
    def insert(self, item: dict, collection_name: str, custom_search: dict = {}):
        collection = self.__db[collection_name]
        return collection.update_one(custom_search,{"$set" : item},upsert=True)
    def find(self, query: dict, collection_name: str) -> list:
        collection = self.__db[collection_name]
        return collection.find_one(query)

