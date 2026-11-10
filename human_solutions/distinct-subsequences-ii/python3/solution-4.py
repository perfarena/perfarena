def sorted_squares(arr):
    n = len(arr)
    neg_ptr = 0
    while neg_ptr < n and arr[neg_ptr] < 0:
        neg_ptr += 1

    pos_ptr = neg_ptr - 1
    sorted_squares = [0] * n
    counter = 0

    while neg_ptr >= 0 and pos_ptr < n:
        if neg_ptr >= 0 and (pos_ptr == n or arr[neg_ptr] ** 2 < arr[pos_ptr] ** 2):
            sorted_squares[counter] = arr[neg_ptr] ** 2
            neg_ptr -= 1
        else:
            sorted_squares[counter] = arr[pos_ptr] ** 2
            pos_ptr += 1
        counter += 1

    while neg_ptr >= 0:
        sorted_squares[counter] = arr[neg_ptr] ** 2
        neg_ptr -= 1
        counter += 1

    while pos_ptr < n:
        sorted_squares[counter] = arr[pos_ptr] ** 2
        pos_ptr += 1
        counter += 1

    return sorted_squares

# Test the function
arr = [-4, -1, 0, 1, 9, 10]
result = sorted_squares(arr)
print(result)