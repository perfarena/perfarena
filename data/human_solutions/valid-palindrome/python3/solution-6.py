def cleanup(string):
    clean_string = ""
    for char in string:
        if char.isalnum():
            clean_string += char.lower()
    return clean_string

def is_palindrome(string):
    left = 0
    right = len(string) - 1
    while left < right:
        if string[left] != string[right]:
            return False
        left += 1
        right -= 1
    return True

def is_valid_palindrome(s):
    cleaned_string = cleanup(s)
    return is_palindrome(cleaned_string)