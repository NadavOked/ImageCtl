"""דגלי יכולת נגזרי-מהדורה. לא הגדרות למפעיל.

#1081: v1 מסתיר כיתות בכל הממשק (קונסולה, תפריט מחשב הבנייה, GUI).
v2 מדליק את CLASSROOMS. ההבדל בין v1 ל-v2 = כיתות בלבד.
"""

# v2 מדליק.
CLASSROOMS = False


def classrooms() -> bool:
    return CLASSROOMS
