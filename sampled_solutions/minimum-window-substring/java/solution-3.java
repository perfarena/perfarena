while (j < S.length()) {
    if (map[S.charAt(j)] > 0) {
        counter++;
    }
    map[S.charAt(j)]--;
    j++;

    while (counter == T.length()) {
        if (minLen > j - i) {
            minLen = j - i;
            start = i;
        }

        if (map[S.charAt(i)] == 0) {
            counter--;
        }
        map[S.charAt(i)]++;
        i++;
    }
}