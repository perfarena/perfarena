def isValidInteger(string_to_test):
    number_seen = False
    i = 0
    
    # Check for sign character at the beginning
    if string_to_test[i] in ['+', '-']:
        i += 1
    
    # Iterate over the characters in the integer
    while i < len(string_to_test):
        current_char = string_to_test[i]
        
        # Check for valid digits
        if not current_char.isdigit():
            return False
        else:
            number_seen = True
        
        i += 1
    
    return number_seen