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
    def find_many(
        self,
        query: dict,
        collection_name: str,
        projection: dict = None,
        sort: list = None,
        skip: int = None,
        limit: int = None,
    ) -> list:
        collection = self.__db[collection_name]
        cursor = collection.find(query, projection or {})
        if sort:
            cursor = cursor.sort(sort)
        if skip:
            cursor = cursor.skip(skip)
        if limit:
            cursor = cursor.limit(limit)
        return list(cursor)

    def count(self, query: dict, collection_name: str) -> int:
        collection = self.__db[collection_name]
        return collection.count_documents(query)

    def distinct(self, field: str, query: dict, collection_name: str) -> list:
        collection = self.__db[collection_name]
        return list(collection.distinct(field, query))
    def create(self, item: dict, collection_name: str):
        collection = self.__db[collection_name]
        return collection.insert_one(item).inserted_id
    def update(self, query: dict, collection_name: str, item: dict):
        collection = self.__db[collection_name]
        return collection.update_one(query, {"$set": item})
    def update_many(self, query: dict, collection_name: str, item: dict):
        collection = self.__db[collection_name]
        return collection.update_many(query, {"$set": item})
    def delete(self, query: dict, collection_name: str):
        collection = self.__db[collection_name]
        return collection.delete_one(query)
