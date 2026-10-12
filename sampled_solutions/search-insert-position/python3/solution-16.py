def search_insert_position(numbers_list, target_value):
    low, high = 0, len(numbers_list)
    
    while low < high:
        middle = (low + high) // 2
        if numbers_list[middle] < target_value:
            low = middle + 1
        else:
            high = middle
    
    return low