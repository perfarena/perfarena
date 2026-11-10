def search_insert_position(numbers_list, target_value):
    for index, num in enumerate(numbers_list):
        if num >= target_value:
            return index
    return len(numbers_list)