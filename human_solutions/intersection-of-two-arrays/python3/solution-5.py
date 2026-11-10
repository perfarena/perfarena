def compute_intersection_optimized(arr1, arr2):
    set_arr2 = set(arr2)
    output = []
    for num in arr1:
        if num in set_arr2:
            output.append(num)
    
    return list(set(output))