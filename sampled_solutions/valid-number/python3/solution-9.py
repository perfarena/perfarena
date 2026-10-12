def isValidNumber(s):
    decimal_used = False
    number_seen = False
    i = 0
    
    # Iterate over the characters in the string
    while i < len(s):
        current_char = s[i]
        
        # Check for valid characters
        if current_char.isalpha() and current_char not in ['e', 'E']:
            return False
        elif current_char in ['+', '-']:
            if i > 0 and s[i - 1] not in ['e', 'E']:
                return False
        elif current_char == '.':
            if decimal_used:
                return False
            decimal_used = True
        elif current_char.isdigit():
            number_seen = True
        else:
            return False
        
        i += 1
    
    # Check if a number was seen
    return number_seen