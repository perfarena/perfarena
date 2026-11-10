def cleanup(string):
    clean_string = ""
    for char in string:
        if char.isalnum():
            clean_string += char.lower()
    return clean_string