def find_highest_altitude(gain):
    result = 0
    total = 0
    
    for n in gain:
        total += n
        result = max(result, total)
    
    return result