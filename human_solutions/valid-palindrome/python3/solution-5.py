def is_valid_palindrome(s):
    cleaned_string = cleanup(s)
    return is_palindrome(cleaned_string)