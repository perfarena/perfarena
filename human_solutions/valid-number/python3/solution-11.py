def isValidNumber(s):
    decimal_used = False
    number_seen = False
    i = 0
    
    while i < len(s):
        current_char = s[i]
        
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
    
    if not number_seen:
        return False
    
    if 'e' in s or 'E' in s:
        e_index = s.index('e') if 'e' in s else s.index('E')
        if not isValidInteger(s[e_index + 1:]):
            return False
    
    return True