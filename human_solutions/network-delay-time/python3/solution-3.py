while lower < upper:
    middle = (upper + lower) // 2
    if letters[middle] > target:
        upper = middle
    else:
        lower = middle + 1
return letters[upper]