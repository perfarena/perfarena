for character in unique:
    start_index = S.find(character)
    end_index = S.rfind(character)
    
    if start_index < end_index:
        result += len(set(S[start_index:end_index + 1]))