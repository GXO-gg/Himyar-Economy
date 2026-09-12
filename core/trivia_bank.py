"""
Himyar Economy — the built-in trivia bank.

Loaded once into guild_id 0 on first start and shared by every server. Staff add
their own with `/trivia add`, and those are scoped to their guild.

Deliberately weighted towards football and general knowledge rather than
Western pop culture, since that's what most of these servers actually talk about.
"""

from __future__ import annotations

BUILTIN_QUESTIONS: list[dict] = [
    {"category": "football", "question": "Which country has won the most FIFA World Cups?",
     "answers": ["Brazil", "Germany", "Italy", "Argentina"], "correct": 0},
    {"category": "football", "question": "Which club is known as 'Los Blancos'?",
     "answers": ["Real Madrid", "Barcelona", "Atletico Madrid", "Valencia"], "correct": 0},
    {"category": "football", "question": "How many players are on the pitch per team in football?",
     "answers": ["11", "10", "12", "9"], "correct": 0},
    {"category": "football", "question": "Which country hosted the 2022 FIFA World Cup?",
     "answers": ["Qatar", "Russia", "Brazil", "UAE"], "correct": 0},
    {"category": "football", "question": "What is the maximum length of a football match's regular time?",
     "answers": ["90 minutes", "80 minutes", "100 minutes", "120 minutes"], "correct": 0},
    {"category": "football", "question": "Which trophy is awarded to Europe's top club side each year?",
     "answers": ["Champions League", "Europa League", "Super Cup", "FA Cup"], "correct": 0},
    {"category": "geography", "question": "What is the capital of Yemen?",
     "answers": ["Sana'a", "Aden", "Taiz", "Hodeidah"], "correct": 0},
    {"category": "geography", "question": "Which is the largest country in the Arabian Peninsula?",
     "answers": ["Saudi Arabia", "Yemen", "Oman", "UAE"], "correct": 0},
    {"category": "geography", "question": "Which sea lies between Africa and the Arabian Peninsula?",
     "answers": ["Red Sea", "Black Sea", "Caspian Sea", "Dead Sea"], "correct": 0},
    {"category": "geography", "question": "What is the longest river in the world?",
     "answers": ["The Nile", "The Amazon", "The Yangtze", "The Mississippi"], "correct": 0},
    {"category": "geography", "question": "Which desert is the largest hot desert on Earth?",
     "answers": ["The Sahara", "The Arabian", "The Gobi", "The Kalahari"], "correct": 0},
    {"category": "geography", "question": "How many continents are there?",
     "answers": ["7", "5", "6", "8"], "correct": 0},
    {"category": "history", "question": "Himyar was an ancient kingdom in which modern country?",
     "answers": ["Yemen", "Oman", "Jordan", "Iraq"], "correct": 0},
    {"category": "history", "question": "Which ancient wonder stood in Alexandria?",
     "answers": ["The Lighthouse", "The Colossus", "The Hanging Gardens", "The Mausoleum"],
     "correct": 0},
    {"category": "science", "question": "What is the chemical symbol for gold?",
     "answers": ["Au", "Ag", "Gd", "Go"], "correct": 0},
    {"category": "science", "question": "How many bones are in the adult human body?",
     "answers": ["206", "186", "226", "196"], "correct": 0},
    {"category": "science", "question": "Which planet is closest to the Sun?",
     "answers": ["Mercury", "Venus", "Mars", "Earth"], "correct": 0},
    {"category": "science", "question": "What gas do plants absorb from the air?",
     "answers": ["Carbon dioxide", "Oxygen", "Nitrogen", "Hydrogen"], "correct": 0},
    {"category": "science", "question": "What is the hardest naturally occurring substance?",
     "answers": ["Diamond", "Steel", "Quartz", "Titanium"], "correct": 0},
    {"category": "general", "question": "How many minutes are in a full day?",
     "answers": ["1440", "1200", "1800", "960"], "correct": 0},
    {"category": "general", "question": "What does 'CPU' stand for?",
     "answers": ["Central Processing Unit", "Computer Personal Unit",
                 "Central Program Utility", "Core Processing Unit"], "correct": 0},
    {"category": "general", "question": "Which language has the most native speakers worldwide?",
     "answers": ["Mandarin Chinese", "English", "Spanish", "Arabic"], "correct": 0},
    {"category": "general", "question": "How many letters are in the Arabic alphabet?",
     "answers": ["28", "26", "30", "24"], "correct": 0},
    {"category": "general", "question": "What is the largest ocean on Earth?",
     "answers": ["Pacific", "Atlantic", "Indian", "Arctic"], "correct": 0},
    {"category": "gaming", "question": "GTA V multiplayer modding platform FiveM is built for which game?",
     "answers": ["Grand Theft Auto V", "Red Dead Redemption 2", "Minecraft", "Rust"],
     "correct": 0},
    {"category": "gaming", "question": "Which company created Discord?",
     "answers": ["Discord Inc.", "Microsoft", "Valve", "Meta"], "correct": 0},
]
