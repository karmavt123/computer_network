# make            build build/main.pdf
# make final      copy it to ../TVI_Technical_Explanation_Report.pdf (the file the brief asks for)
# make inputs     regenerate tables / snippets / figures from the model
# make clean
all:
	latexmk main.tex
final: all
	cp build/main.pdf ../TVI_Technical_Explanation_Report.pdf
inputs:
	cd ../.. && python3 Tools/gen_configs.py && python3 Tools/gen_docs.py && python3 Tools/gen_latex.py
clean:
	latexmk -C main.tex; rm -rf build
.PHONY: all final inputs clean
