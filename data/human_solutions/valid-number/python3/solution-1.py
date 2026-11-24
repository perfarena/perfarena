def isNumber(s):
    if s[0] in ['+', '-']:
        s = s[1:]
    
    if '.' in s:
        parts = s.split('.')
        if len(parts) != 2:
            return False
        if not parts[0].isdigit() or not parts[1].isdigit():
            return False
    else:
        if not s.isdigit():
            return False
    
    return True

# Test the function
print(isNumber("123"))  # True
print(isNumber("-123"))  # True
print(isNumber("12.34"))  # True
print(isNumber("12."))  # False
print(isNumber(".12"))  # False