def max_elements(arr):
    aa = 0
    j = 0
    k = 2  # Initial value of k
    max_count = 0

    for num in arr:
        if num == 0 and k > 0:
            k -= 1
            j += 1
        elif k < 0:
            if arr[aa] == 0:
                k += 1
            aa += 1
        else:
            j += 1

        max_count = max(max_count, j - aa)

    return max_count

# Test the function with the input array
arr = [0, 1, 0, 0, 1, 1, 0, 1, 1, 0]
result = max_elements(arr)
print(result)