def check_sequence_advanced(s, t):
    indices = {}
    for i, char in enumerate(t):
        if char not in indices:
            indices[char] = []
        indices[char].append(i)
    
    prev_index = -1
    for char in s:
        if char not in indices:
            return False
        found_index = next((index for index in indices[char] if index > prev_index), None)
        if found_index is None:
            return False
        prev_index = found_index
    
    return True