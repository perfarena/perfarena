while (lower < upper) {
    let middle = Math.floor((upper + lower) / 2);
    if (letters[middle] > target) {
        upper = middle;
    } else {
        lower = middle + 1;
    }
}
return letters[upper];