function isSubsequence(s, T) {
    if (s.length === 0) {
        return true;
    }

    let pointer1 = 0;
    let pointer2 = 0;

    while (pointer1 < s.length && pointer2 < T.length) {
        if (s[pointer1] !== T[pointer2]) {
            while (T[pointer2] !== s[pointer1]) {
                pointer2++;
                if (pointer2 === T.length) {
                    return false;
                }
            }
        }
        pointer1++;
        pointer2++;
    }

    return pointer1 === s.length;
}