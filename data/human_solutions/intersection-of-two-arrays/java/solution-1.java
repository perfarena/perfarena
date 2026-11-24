import java.util.*;

public class UniqueElementsFinder {
    public int[] findUniqueElements(int[] nums1, int[] nums2) {
        Set<Integer> set1 = new HashSet<>();
        Set<Integer> set2 = new HashSet<>();

        for (int num : nums1) {
            set1.add(num);
        }

        for (int num : nums2) {
            set2.add(num);
        }

        List<Integer> resultList = new ArrayList<>();
        for (int num : set1) {
            if (!set2.contains(num)) {
                resultList.add(num);
            }
        }

        for (int num : set2) {
            if (!set1.contains(num)) {
                resultList.add(num);
            }
        }

        int[] resultArray = new int[resultList.size()];
        for (int i = 0; i < resultList.size(); i++) {
            resultArray[i] = resultList.get(i);
        }

        return resultArray;
    }

    public static void main(String[] args) {
        UniqueElementsFinder finder = new UniqueElementsFinder();
        int[] nums1 = {1, 2, 3, 4, 5};
        int[] nums2 = {3, 4, 5, 6, 7};
        int[] uniqueElements = finder.findUniqueElements(nums1, nums2);

        System.out.println("Unique Elements:");
        for (int num : uniqueElements) {
            System.out.print(num + " ");
        }
    }
}