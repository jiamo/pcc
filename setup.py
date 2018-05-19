from setuptools import setup, find_packages

scripts = []


setup(
    name="Pcc",
    version='0.1.1',
    license = 'BSD License',
    packages = find_packages(exclude=['tests']),
    zip_safe = False,
    include_package_data=True,
    package_data = {
        '':['*.md', '*.rst','MANIFEST.in']
        },  ## this seem no useness
    keywords = 'c compiler llvm ply',
    scripts= scripts ,
    url='http://pypi.python.org/pypi/pcc/',
    description='Pcc is a c compler build on python and llvm.',
    long_description=open('README.md').read(),
    classifiers = [
        'License :: No License',
        'Intended Audience :: Programer',
        'Development Status :: 6 - Mature',
        'Programming Language :: Python :: 3',
        'Operating System :: OS Independent',
        'Topic :: C compiler',
        ],
    )
