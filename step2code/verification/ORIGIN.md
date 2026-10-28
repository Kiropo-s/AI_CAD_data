# Geometry evaluator origin

The numeric core was moved from the workspace json2scapi v4 evaluator at baseline commit 097478e. The implementation now belongs to step2code and has no runtime imports from json2scapi. Unused registration and legacy comparison APIs were removed.

Fixed source coordinates, shared numerical conditioning, 8192 area-weighted surface samples and acceptance thresholds are retained. New reliability checks reject occupied-space unions that violate input volume bounds. Any reliable failed required metric takes priority over an unrelated metric error. These policy changes are included in every run fingerprint.
