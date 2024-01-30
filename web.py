from flask import Flask, request, jsonify, render_template
from openai import OpenAI
from dotenv import load_dotenv
from neo4j import GraphDatabase
import concurrent.futures
import time
import requests
import csv
import re
import json

load_dotenv("key.env")

app = Flask(__name__)
client = OpenAI(api_key="OpenAI_Key")
graph_thread = client.beta.threads.create()
chat_thread = client.beta.threads.create()
first_message = None


@app.route('/')
def home():
    return render_template('chat.html')


@app.route('/ask', methods=['POST'])
def ask():
    user_message = request.form['message']
    try:
        client.beta.threads.messages.create(
            thread_id=chat_thread.id,
            role="user",
            content=user_message
        )
        run = client.beta.threads.runs.create(
            thread_id=chat_thread.id,
            assistant_id="asst_SMZCTdRhGaipt39NloQe6GnA"
        )

        url = f"https://api.openai.com/v1/threads/{chat_thread.id}/runs/{run.id}"
        headers = {
            "Authorization": "Bearer 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",
            "OpenAI-Beta": "assistants=v1"
        }
        while not requests.get(url, headers=headers).json().get('status') == "completed":
            time.sleep(.3)

        # Extracting the AI response text
        messages = client.beta.threads.messages.list(thread_id=chat_thread.id)
        last_assistant_message = [m for m in messages if m.role == "assistant"][0]
        message_text = last_assistant_message.content[0].text.value
        print(message_text)
        return jsonify({"response": message_text})
    except Exception as e:
        print(e)
        return jsonify({'error': 'There was an error processing your request.'}), 500


@app.route('/generate_graph', methods=['POST'])
def generate_graph():
    user_message = request.form['message']
    try:
        # Call Nodes Assistant
        nodes, node_file_list = call_assistant_and_save_files("asst_QFZns8bDkOiHlgE9BrPgjqDh", graph_thread.id, user_message)
        # Call Edges Assistant
        _, edge_file_list = call_assistant_and_save_files("asst_7jhwftzN0smaOZ6HNXLZMCm6", graph_thread.id, nodes)
        ask_neo4j(node_file_list, edge_file_list)
        return jsonify({"response":"Your graph has been generated!"})
    except Exception as e:
        print(e)
        return jsonify({'error': 'There was an error processing your request.'}), 500


@app.route('/ask_rest', methods=['POST'])
def ask_neo4j(node_list, edge_list):
    conn = Neo4jConnection(uri="INSERT_URL", user="INSERT_USERNAME",
                           pwd="INSERT_PASSWORD")

    delete_stuff = """MATCH(n) 
    DETACH DELETE n"""
    conn.query(delete_stuff)
    print(node_list)
    for node in node_list:
        if node is None:
            continue
        # node = node.replace(" ", "%20")
        # Nodes
        if node == "Books.csv":
            query = f'''
            LOAD CSV WITH HEADERS FROM "file:///{node}" AS row
            CREATE (:Book {{id: row.ID, title: row.Title, author: row.Author}});
            '''
            conn.query(query)

        if node == "Historical_Figures_or_Fictional_Characters.csv":
            query = f'''
            LOAD CSV WITH HEADERS FROM "file:///{node}" AS row
            CREATE (:Person {{id: row.ID, name: row.Name, label: row.Label}});
            '''
            conn.query(query)

        if node == "Religious_Scriptures.csv":
            query = f'''
            LOAD CSV WITH HEADERS FROM "file:///{node}" AS row
            CREATE (:Scripture {{id: row.ID, name: row.Name, significance: row.Significance}});
            '''
            conn.query(query)

        if node == "Illustrative_Stories.csv":
            query = f'''
            LOAD CSV WITH HEADERS FROM "file:///{node}" AS row
            CREATE (:Story {{id: row.ID, name: row.Name, origin: row.Origin}});
            '''
            conn.query(query)

        if node == "Concept.csv":
            query = f'''
            LOAD CSV WITH HEADERS FROM "file:///{node}" AS row
            CREATE (:Concept {{id: row.ID, name: row.Name, description: row.Description}});
            '''
            conn.query(query)

    for edge in edge_list:
        #Edges
        if edge is None:
            continue
        query = f"""
        LOAD CSV FROM "file:///{edge}" AS row FIELDTERMINATOR ','
        MATCH (source)
        WHERE (source:Book AND source.id = row[0]) OR (source:Story AND source.id = row[0]) OR (source:Person AND source.id = row[0]) OR (source:Scripture AND source.id = row[0]) OR (source:Concept AND source.id = row[0])
        MATCH (target)
        WHERE (target:Book AND target.id = row[1]) OR (target:Story AND target.id = row[1]) OR (target:Person AND target.id = row[1]) OR (target:Scripture AND target.id = row[1]) OR (target:Concept AND target.id = row[1])
        CREATE (source)-[:{edge.replace(".csv", "")}]->(target)
        """
        conn.query(query)

    display = """
    MATCH (n)
    OPTIONAL MATCH (n)-[r]-()
    RETURN n, r
    """
    conn.query(display)


def fetch_graph_data(driver):
    query = """
    MATCH (n)
    OPTIONAL MATCH (n)-[r]-()
    RETURN n, r
    """
    with driver.session() as session:
        result = session.run(query)
        return [{"node": record["n"].id, "relationship": record["r"].id if record["r"] else None} for record in result]


def fetch_and_save_graph(driver):
    graph_data = fetch_graph_data(driver)

    # Save data to a file
    with open('graph_data.json', 'w') as file:
        json.dump(graph_data, file)

    # Close Neo4j driver
    driver.close()


def save_csv_files(input_string, path=""):
    # Split the input string into lines
    lines = input_string.split('\n')
    filename_list = []
    for line in lines:
        # Split each line into words
        words = line.split()

        if len(words) > 1:
            # The first word is the file name
            filename_list.append(words[0])
            file_name = path + words[0]
            # The remaining words are data pairs
            data_pairs = [pair.split(',') for pair in words[1:]]

            # Write the data pairs to a CSV file
            with open(file_name, 'w', newline='') as file:
                writer = csv.writer(file)
                for pair in data_pairs:
                    writer.writerow(pair)
    return filename_list


def save_csv_files_unclean(input_string, path=""):
    # Split the input string into lines
    lines = input_string.split('\n')

    # Initialize variables
    file_name = None
    data_pairs = []
    filename_list = []

    for line in lines:
        # Check if the line contains a file name
        if '.csv' in line:
            # If there's an existing file name, it means we've reached a new file
            # Save the current data pairs to the current file before continuing
            if file_name:
                write_to_csv(file_name, data_pairs)

            # Extract and clean the file name
            filename_list.append(re.findall(r'\b\w+\.csv\b', line)[0])
            file_name = path + re.findall(r'\b\w+\.csv\b', line)[0]
            data_pairs = []

        # Check if the line contains data pairs
        elif ',' in line and file_name:
            # Add the data pairs to the list
            pairs = re.findall(r'\b\w+,\w+\b', line)
            data_pairs.extend([pair.split(',') for pair in pairs])

    # Write the last batch of data pairs to a CSV file
    if file_name:
        write_to_csv(file_name, data_pairs)
    return filename_list


def save_csv_files_multi_column(input_string, path=""):
    # Split the input string into lines
    lines = input_string.split('\n')

    # Initialize variables
    file_name = None
    data_rows = []
    filename_list = []

    for line in lines:
        # Check if the line contains a file name
        if '.csv' in line:
            # If there's an existing file name, it means we've reached a new file
            # Save the current data rows to the current file before continuing
            if file_name:
                write_to_csv(file_name, data_rows)

            # Extract and clean the file name
            filename_list.append(re.findall(r'\b\w+\.csv\b', line)[0])
            file_name = path + re.findall(r'\b\w+\.csv\b', line)[0]
            data_rows = []

        elif len(line) > 80:
            continue

        # Check if the line contains data rows
        elif ',' in line and file_name:
            if ", " in line:
                line = line.replace(", ", " ")

            # Add the data row to the list, splitting by commas
            row = line.strip().split(',')
            if len(row) > 1:  # Ensure the row has at least two columns
                data_rows.append(row)

    # Write the last batch of data rows to a CSV file
    if file_name:
        write_to_csv(file_name, data_rows)
    return filename_list


def write_to_csv(file_name, data_pairs):
    # Write the data pairs to a CSV file
    with open(file_name, 'w', newline='') as file:
        writer = csv.writer(file)
        for pair in data_pairs:
            writer.writerow(pair)


def call_assistant_and_save_files(asst_id, thrd_id, usr_message, nodes=False):
    client.beta.threads.messages.create(
        thread_id=thrd_id,
        role="user",
        content=usr_message
    )

    run = client.beta.threads.runs.create(
        thread_id=thrd_id,
        assistant_id= asst_id
    )

    url = f"https://api.openai.com/v1/threads/{thrd_id}/runs/{run.id}"
    headers = {
        "Authorization": "Bearer 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",
        "OpenAI-Beta": "assistants=v1"
    }
    while not requests.get(url, headers=headers).json().get('status') == "completed":
        time.sleep(.3)

    # Extracting the AI response text
    messages = client.beta.threads.messages.list(thread_id=thrd_id)
    last_assistant_message = [m for m in messages if m.role == "assistant"][0]
    message_text = last_assistant_message.content[0].text.value
    print(message_text)

    path = "/Users/jeffolmo/Library/Application Support/Neo4j Desktop/Application/relate-data/dbmss/dbms-f994eaf3-f7f2-4983-ba1c-13bafa1ba16d/import/"
    if nodes:
        filename_list = save_csv_files_unclean(message_text, path)
    else:
        filename_list = save_csv_files_multi_column(message_text, path)
    return message_text, filename_list


class Neo4jConnection:

    def __init__(self, uri, user, pwd):
        self.__uri = uri
        self.__user = user
        self.__pwd = pwd
        self.__driver = None
        try:
            self.__driver = GraphDatabase.driver(self.__uri, auth=(self.__user, self.__pwd))
        except Exception as e:
            print("Failed to create the driver:", e)

    def close(self):
        if self.__driver is not None:
            self.__driver.close()

    def query(self, query, parameters=None, db=None):
        assert self.__driver is not None, "Driver not initialized!"
        session = None
        response = None
        try:
            session = self.__driver.session(database=db) if db is not None else self.__driver.session()
            response = list(session.run(query, parameters))
        except Exception as e:
            print("Query failed:", e)
        finally:
            if session is not None:
                session.close()
        return response


if __name__ == '__main__':
    app.run(debug=True)



