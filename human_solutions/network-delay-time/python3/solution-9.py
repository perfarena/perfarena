def find_smallest_letter(target, letters):
    L, R = 0, len(letters) - 1
    answer = 0
    
    while L <= R:
        mid = L + (R - L) // 2
        
        if letters[mid] == target:
            L = mid + 1
        elif letters[mid] > target:
            answer = mid
            R = mid - 1
        else:
            L = mid + 1
    
    return letters[answer] if answer < len(letters) else letters[0]